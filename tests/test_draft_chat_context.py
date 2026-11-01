"""Desk draft to Chat context, without suppliers or model calls."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from tests import ROOT
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.draft_workflow import _plan, _write
from researchhelper.contracts import ContractError, canonical
from researchhelper.evidence import available_security_codes

PAPER=dict(title='合成研究底稿',conclusion='这是用于核对上下文的测试文本，不是金融判断。',
    logics=[dict(title='逻辑甲',analysis='分析甲',implication='判断甲',risk='反证甲'),
            dict(title='逻辑乙',analysis='分析乙的原始说明',implication='判断乙',risk='反证乙'),
            dict(title='逻辑丙',analysis='分析丙',implication='判断丙',risk='反证丙')],risks=['测试材料'])


class DraftChatContextTests(unittest.TestCase):
    def setUp(self):
        blocked=patch.object(socket.socket,'connect',side_effect=AssertionError('No external calls'))
        blocked.start();self.addCleanup(blocked.stop)
        self.folder=tempfile.TemporaryDirectory(prefix='rh-draft-chat-');self.addCleanup(self.folder.cleanup)
        self.agent=ResearchAgent(ResearchArchive(self.folder.name),ROOT,offline=True,draft_first=True,require_argument_support=True)
        self.addCleanup(self.agent.close_runtime)

    def parent(self,paper=None):
        task=self.agent.create('研究600519.SH的合成上下文案例',objects=['600519.SH'],research_mode='current')
        return self.agent.start(task['id'],dict(draft_report=copy.deepcopy(paper or PAPER),data_requests=[]))

    def test_followup_reads_displayed_draft_and_preserves_frozen_parent(self):
        parent=self.parent();before=canonical(self.agent.archive.get(parent['id']))
        html=self.agent.archive.report(parent['id'],parent['delivery']['version'])
        child=self.agent.follow_up(parent['id'],'展开逻辑2',start=False)
        context=child['conversation_context'];text=context['previous_answer']['text']
        self.assertIn('分析乙的原始说明',text);self.assertIn('尚未独立复核',text)
        self.assertEqual(context['parent_binding']['delivery_digest'],parent['delivery']['digest'])
        self.assertEqual(canonical(self.agent.archive.get(parent['id'])),before)
        self.assertEqual(self.agent.archive.report(parent['id'],parent['delivery']['version']),html)
        for key in ('evidence','source_records','calculations','theses'):self.assertEqual(child[key],[])

    def test_long_first_argument_does_not_hide_explicit_second_argument(self):
        paper=copy.deepcopy(PAPER);paper['logics'][0]['analysis']='拉长的测试段落'*1000
        parent=self.parent(paper)
        for message in ['展开逻辑2','展开逻辑二','分析策略逻辑二']:
            child=self.agent.follow_up(parent['id'],message,start=False)
            self.assertIn('分析乙的原始说明',child['conversation_context']['previous_answer']['text'])

    def test_planner_and_writer_receive_the_same_bound_readonly_context(self):
        child=self.agent.follow_up(self.parent()['id'],'展开逻辑2',start=False)
        captured=[]
        class Capture:
            def reason(self,task,messages,**options):
                captured.append((options['phase'],json.loads(messages[1]['content']),messages[0]['content']))
                return '{"data_requests":[]}' if options['phase']=='draft_plan' else json.dumps(PAPER)
        engine=SimpleNamespace(model=Capture())
        _plan(engine,child,1);_write(engine,child,[])
        self.assertEqual([row[0] for row in captured],['draft_plan','draft_report'])
        for phase,payload,rule in captured:
            self.assertEqual(payload['conversation_context'],child['conversation_context'])
            self.assertIn('分析乙',payload['conversation_context']['previous_answer']['text'])
            self.assertIn('不是本版事实',rule)
        self.assertEqual(child['ledger']['used']['model_calls'],0)

    def test_stale_context_is_rejected_before_model_dispatch(self):
        child=self.agent.follow_up(self.parent()['id'],'展开逻辑2',start=False)
        child['conversation_context']['parent_binding']['task_id']='different'
        class Never:
            def reason(self,*args,**kwargs):raise AssertionError('Should not dispatch')
        for call in [lambda:_plan(SimpleNamespace(model=Never()),child,1),
                     lambda:_write(SimpleNamespace(model=Never()),child,[])]:
            with self.assertRaises(ContractError):call()

    def test_historical_followup_preserves_the_declared_cutoff(self):
        task=self.agent.create('按历史时点研究600519.SH',objects=['600519.SH'],
            cutoff='2026-06-30T20:00:00+08:00',research_mode='historical')
        parent=self.agent.start(task['id'],dict(draft_report=PAPER,data_requests=[]))
        child=self.agent.follow_up(parent['id'],'展开逻辑2',start=False)
        self.assertEqual(child['research_mode'],'historical')
        self.assertEqual(child['cutoff'],parent['cutoff'])

    def test_replacing_subject_does_not_turn_old_draft_into_new_evidence(self):
        child=self.agent.follow_up(self.parent()['id'],'改看600036.SH的风险',start=False)
        self.assertEqual(child['objects'],['600036.SH'])
        self.assertEqual(available_security_codes(child),['600036.SH'])
        self.assertEqual(child['conversation_context']['scope_relation'],'replace')
        self.assertEqual(child['evidence'],[])


if __name__=='__main__':unittest.main()
