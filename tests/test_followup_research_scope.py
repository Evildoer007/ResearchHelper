"""A new Chat question is not constrained by its parent's research intent."""
from copy import deepcopy
import json,socket,tempfile,unittest
from types import SimpleNamespace
from unittest.mock import patch
from lxml import html
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ENTRIES,INTENTS,DOCUMENT_CONTENT_INTENT,canonical
from researchhelper.engine import ResearchAgent
from researchhelper.planning import draft_research_scope,input_fingerprint
from researchhelper.draft_workflow import _plan,_write
from researchhelper.drafts import normalize_report
from researchhelper.evidence import available_security_codes
from researchhelper.report_designer import design

QUESTION='改为比较ETF510300.SH和ETF510500.SH的费率，只核对事实，不要投资建议。'
PAPER=dict(title='费用资料比较',report_scope='comparison',conclusion='费用与收益不是同一概念。',
    logics=[dict(title='费用条件',analysis='只对照实际取得的费用条件，不补造费率。')])


class FollowupResearchScopeTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='followup-scope-',dir=ROOT/'result/acceptance')
        self.addCleanup(temporary.cleanup);self.archive=ResearchArchive(temporary.name)
        self.agent=ResearchAgent(self.archive,ROOT,offline=True,draft_first=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('No external request'))
        guard.start();self.addCleanup(guard.stop)
    def parent(self,entry='个股',intent='发现投资机会'):
        return self.agent.create('研究600519.SH的投资机会',entry=entry,intent=intent,objects=['600519.SH'],research_mode='current')
    def assert_fresh_scope(self,child):
        scope=draft_research_scope(child)
        self.assertEqual(scope['mode'],'by_question')
        self.assertEqual(scope['allowed_report_scopes'],['fact','comparison','mechanism','research'])
        self.assertEqual(child['intent'],'综合研究')
        self.assertEqual(child['input_hash'],input_fingerprint(child))
        self.assertEqual(child['original_input']['intent'],child['intent'])
        self.assertEqual(child['original_input']['entry'],child['entry'])
    def test_switching_from_stock_opportunity_to_etf_comparison_drops_the_old_framework(self):
        parent=self.parent();child=self.agent.follow_up(parent['id'],QUESTION,start=False)
        self.assert_fresh_scope(child)
        self.assertEqual(child['entry'],'混合')
        self.assertEqual(child['objects'],['510300.SH','510500.SH'])
        self.assertEqual(available_security_codes(child),child['objects'])
        self.assertEqual(normalize_report(PAPER,child,[])['organization'],'comparison')
    def test_all_eight_entries_and_five_old_intents_leave_the_new_question_free(self):
        for entry in ENTRIES:
            for intent in INTENTS:
                if intent==DOCUMENT_CONTENT_INTENT:continue
                with self.subTest(entry=entry,intent=intent):
                    parent=self.parent(entry,intent);before=canonical(parent)
                    child=self.agent.follow_up(parent['id'],QUESTION,start=False)
                    self.assert_fresh_scope(child)
                    self.assertEqual(child['entry'],'混合')
                    self.assertEqual(self.archive.get(parent['id']),parent)
                    self.assertEqual(canonical(parent),before)
    def test_continuing_an_object_keeps_the_known_entry_but_not_an_old_opportunity_obligation(self):
        for entry in ENTRIES:
            with self.subTest(entry=entry):
                parent=self.parent(entry);child=self.agent.follow_up(parent['id'],'只核对它前两年的收入读数。',start=False)
                self.assert_fresh_scope(child)
                self.assertEqual(child['entry'],entry)
                self.assertEqual(child['objects'],parent['objects'])
                self.assertEqual(child['conversation_context']['scope_relation'],'continue')
                self.assertEqual(normalize_report(dict(PAPER,report_scope='fact'),child,[])['organization'],'fact')
    def test_a_prior_comparison_does_not_force_a_new_mechanism_question_to_remain_a_comparison(self):
        parent=self.parent(intent='比较对象')
        child=self.agent.follow_up(parent['id'],'解释它的利润变化可能通过哪些机制发生。',start=False)
        self.assert_fresh_scope(child)
        self.assertEqual(normalize_report(dict(PAPER,report_scope='mechanism'),child,[])['organization'],'mechanism')
    def test_adding_an_object_is_mixed_and_keeps_both_requested_objects(self):
        parent=self.parent();child=self.agent.follow_up(parent['id'],'把它与ETF510300.SH加入比较，只核对数据。',start=False)
        self.assert_fresh_scope(child)
        self.assertEqual(child['entry'],'混合')
        self.assertEqual(child['conversation_context']['scope_relation'],'compare')
        self.assertEqual(child['objects'],['600519.SH','510300.SH'])
    def test_a_switch_by_name_does_not_keep_a_stale_stock_family_or_invent_a_code(self):
        child=self.agent.follow_up(self.parent()['id'],'改为研究一只新的ETF，先核对名称和公开资料。',start=False)
        self.assert_fresh_scope(child)
        self.assertEqual(child['entry'],'混合');self.assertEqual(child['objects'],[])
        self.assertEqual(available_security_codes(child),[])
    def test_document_only_purpose_is_not_silently_imposed_on_a_new_research_question(self):
        parent=self.parent(intent=DOCUMENT_CONTENT_INTENT)
        child=self.agent.follow_up(parent['id'],'改为研究ETF510300.SH的风险，不引用上一份原件作事实。',start=False)
        self.assert_fresh_scope(child)
        for key in ('source_records','materials','evidence','calculations','theses'):self.assertEqual(child[key],[])
        self.assertIn('不是当前指令',child['conversation_context']['rule'])
    def test_parent_budget_and_saved_report_are_unchanged_when_the_new_comparison_is_delivered(self):
        parent=self.agent.start(self.parent()['id'],dict(data_requests=[],draft_report=dict(title='离线父报告',
            logics=[dict(title='原研究范围',analysis='只用于测试父报告保持不变。')])))
        original_task=canonical(self.archive.get(parent['id']));original=deepcopy(self.archive.report(parent['id'],1))
        child=self.agent.follow_up(parent['id'],QUESTION,start=False)
        done=self.agent.start(child['id'],dict(data_requests=[],draft_report=PAPER))
        self.assertTrue(done.get('delivery'),done.get('error'))
        self.assertEqual(done['draft_report']['organization'],'comparison')
        self.assertEqual(canonical(self.archive.get(parent['id'])),original_task)
        self.assertEqual(self.archive.report(parent['id'],1),original)
        self.assertEqual(done['ledger']['used']['model_calls'],0);self.assertEqual(done['ledger']['used']['data_requests'],0)
        for profile in ('standard','detailed'):
            document=design(self.archive.report(done['id'],1)['content'],ROOT,profile=profile)
            self.assertNotIn('策略逻辑',html.fromstring(document['html']).find('.//main').text_content())
    def test_both_reasoning_phases_receive_the_new_scope_and_the_bound_parent(self):
        child=self.agent.follow_up(self.parent()['id'],QUESTION,start=False);captured=[]
        class Capture:
            def reason(self,task,messages,**options):
                captured.append((options['phase'],json.loads(messages[1]['content'])))
                return json.dumps(dict(data_requests=[])) if options['phase']=='draft_plan' else json.dumps(PAPER)
        engine=SimpleNamespace(model=Capture());_plan(engine,child,1);_write(engine,child,[])
        self.assertEqual([row[0] for row in captured],['draft_plan','draft_report'])
        for phase,payload in captured:
            self.assertEqual(payload['research_scope'],draft_research_scope(child))
            self.assertEqual(payload['conversation_context'],child['conversation_context'])
    def test_historical_followup_keeps_its_cutoff_while_the_question_changes(self):
        parent=self.agent.create('按历史时点研究600519.SH',entry='个股',intent='发现投资机会',objects=['600519.SH'],
            cutoff='2026-06-30T18:00:00+08:00',research_mode='historical')
        child=self.agent.follow_up(parent['id'],QUESTION,start=False)
        self.assert_fresh_scope(child)
        self.assertEqual(child['research_mode'],'historical');self.assertEqual(child['cutoff'],parent['cutoff'])

    def test_followup_explicit_cutoff_changes_only_the_related_task(self):
        parent = self.agent.create('核对600519.SH的行情。',objects=['600519.SH'],
            cutoff='2026-10-16T18:00:00+08:00',research_mode='historical')
        parent = self.agent.start(parent['id'],dict(draft_report=PAPER,data_requests=[]))
        old_report = self.archive.report(parent['id'],parent['version'])
        child = self.agent.follow_up(parent['id'],'把研究截止时点改为2026-10-15T06:00:00Z，继续核对它的行情。',start=False)
        self.assertEqual((child['research_mode'],child['cutoff']),('historical','2026-10-15T06:00:00Z'))
        self.assertEqual(child['conversation_parent']['delivery']['digest'],old_report['digest'])
        self.assertEqual(child['objects'],['600519.SH'])
        self.assertEqual(child['source_records'],[])
        self.assertEqual(self.archive.report(parent['id'],parent['version']),old_report)
    def test_loading_an_already_saved_task_does_not_rewrite_its_old_intent(self):
        task=self.agent.create(QUESTION,entry='个股',intent='发现投资机会',objects=['510300.SH','510500.SH'])
        before=canonical(task);reopened=ResearchAgent(self.archive,ROOT,offline=True,draft_first=True,require_argument_support=True)
        try:
            old=reopened.archive.get(task['id'])
            self.assertEqual(canonical(old),before)
            self.assertEqual(draft_research_scope(old)['allowed_report_scopes'],['research'])
        finally:reopened.close_runtime()

if __name__=='__main__':unittest.main()
