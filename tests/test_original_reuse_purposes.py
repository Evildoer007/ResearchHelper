"""Every original reuse keeps its question and purpose without another download.

The iFinD case reads an unchanged retained BYD task. The official-channel
cases use explicit synthetic HTML transports in a disposable Store. Neither
fixture approves financial claims or proves current supplier permissions.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical
from researchhelper.draft_workflow import _writing_messages
from researchhelper.engine import ResearchAgent
from researchhelper.evidence import EvidenceCollector
from researchhelper.official_sources import release_choices
from researchhelper.planning import draft_research_context

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = Path(os.environ.get('RESEARCHHELPER_TEST_RESOURCES', ROOT))
SOURCE = ROOT/'result/acceptance/one-sentence-research-bx8iekji/task.json'
URL = 'https://www.stats.gov.cn/sj/zxfbhjd/202609/t20260930_1965449.html'
CATALOG = ('<a href="'+URL+'">2026年9月中国采购经理指数运行情况</a>').encode()
ORIGINAL = ('<html><head><meta name="PubDate" content="2026/09/30 09:30"></head>'
    '<body><h1>2026年9月中国采购经理指数运行情况</h1><p>合成原件仅核对内容及适用条件。</p></body></html>').encode()


def raw_identity(record):
    return {key:deepcopy(record.get(key)) for key in
            ('id','snapshot','snapshot_hash','hash','parameters','request_hash','directory','acquisition','status')}


class OriginalReusePurposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == cls.original_hash

    def setUp(self):
        network = patch.object(socket.socket, 'connect', side_effect=AssertionError('No external calls'))
        network.start()
        self.addCleanup(network.stop)

    def ifind(self):
        task = json.loads(SOURCE.read_text())
        record = next(row for row in task['source_records'] if row.get('kind') == 'filing')
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.collector = Mock()
        agent.source_credentials = {}
        for name in ('_journal','_active','_save_model_checkpoint','_gap'):
            setattr(agent, name, Mock())
        return agent, task, record

    def official(self, agent=None, *, mock_reuse=True):
        if agent is None:
            folder = tempfile.TemporaryDirectory(prefix='reuse-purpose-', dir=ROOT/'result/acceptance')
            self.addCleanup(folder.cleanup)
            agent = ResearchAgent(ResearchArchive(folder.name), RESOURCES, offline=True, draft_first=True)
            self.addCleanup(agent.close_runtime)
        task = agent.create('核对发布原文及适用条件', entry='宏观', intent='综合研究', research_mode='current',
            objects=['中国采购经理指数'], questions=[
                dict(id='q1', question='原文披露了什么', kind='fact'),
                dict(id='q2', question='原文说明了哪些适用条件', kind='fact')])
        task['synthetic'] = True
        collector = EvidenceCollector(offline=False)
        with patch('researchhelper.source_transport.official_html', return_value=CATALOG):
            catalog = collector.external(task,'official','release_catalog',dict(channel='nbs_release',page='0'),{})
        task['source_records'].append(catalog)
        choice = release_choices(task)[0]
        parameters = {key:choice[key] for key in ('catalog_id','release_id')}
        with patch('researchhelper.source_transport.official_html', return_value=ORIGINAL):
            record = collector.external(task,'official','release_original',parameters,{})
        record.update(data_need_ids=['initial_original'],question_ids=['q1'],selection_reason='核对合成原文内容。')
        task['source_records'].append(record)
        if mock_reuse:
            agent.collector = Mock()
            agent._journal = Mock()
            agent._save_model_checkpoint = Mock()
            agent._active = Mock(return_value=task)
        return agent, task, record

    def request(self, task, record, ident='reuse_original', question_ids=None, reason='复用原件，核对本问题的适用条件。'):
        return dict(id=ident,source=record['source'],operation=record['operation'],
            parameters=deepcopy(record['parameters']),question_ids=question_ids or [task['questions'][0]['id']],
            selection_reason=reason)

    def assert_reuse(self, agent, task, record, need):
        ledger = deepcopy(task['ledger'])
        original = raw_identity(record)
        count = len(task['source_records'])
        agent._acquire_sources(task, [need])
        agent.collector.external.assert_not_called()
        self.assertEqual(task['ledger'], ledger)
        self.assertEqual(raw_identity(record), original)
        self.assertEqual(len(task['source_records']), count)
        self.assertIn(need['id'], record['data_need_ids'])
        self.assertTrue(set(need['question_ids']) <= set(record['question_ids']))
        rows = [row for row in record.get('request_purposes',[]) if row.get('data_need_id') == need['id']]
        self.assertEqual(rows, [dict(data_need_id=need['id'],question_ids=need['question_ids'],selection_reason=need['selection_reason'])])
        context = draft_research_context(task)
        rows = [row for row in context['source_purposes'] if row.get('data_need_id') == need['id']]
        self.assertEqual(rows, [dict(source_id=record['id'],question_ids=need['question_ids'],purpose=need['selection_reason'],data_need_id=need['id'])])
        self.assertFalse(context['automatic_plan_change'] or context['financial_approval'])
        self.assertEqual(context['extra_model_calls'],0)

    def test_retained_ifind_original_keeps_the_new_question_purpose(self):
        agent,task,record = self.ifind()
        self.assert_reuse(agent,task,record,self.request(task,record))

    def test_official_original_keeps_the_new_question_purpose(self):
        agent,task,record = self.official()
        self.assert_reuse(agent,task,record,self.request(task,record))

    def test_two_new_questions_keep_distinct_purposes_for_one_original(self):
        agent,task,record = self.official()
        for ident, question, reason in [('content_use','q1','核对原文披露内容。'),('condition_use','q2','核对原文适用条件。')]:
            self.assert_reuse(agent,task,record,self.request(task,record,ident,[question],reason))
        self.assertEqual(record['selection_reason'],'核对合成原文内容。')

    def test_repeated_same_reuse_does_not_duplicate_its_purpose(self):
        agent,task,record = self.official()
        need = self.request(task,record)
        self.assert_reuse(agent,task,record,need)
        metadata = deepcopy((record['request_purposes'],record['data_need_ids'],record['question_ids']))
        self.assert_reuse(agent,task,record,need)
        self.assertEqual((record['request_purposes'],record['data_need_ids'],record['question_ids']),metadata)

    def test_legacy_metadata_keeps_its_original_purpose_without_inventing_alias_pairings(self):
        agent,task,record = self.official()
        record['data_need_ids'].append('legacy_alias_without_own_reason')
        self.assert_reuse(agent,task,record,self.request(task,record))
        old, new = record['request_purposes']
        self.assertIsNone(old['data_need_id'])
        self.assertEqual(old['selection_reason'],'核对合成原文内容。')
        self.assertEqual(new['data_need_id'],'reuse_original')

    def test_current_purposes_reach_writing_without_a_model_call(self):
        agent,task,record = self.official()
        need = self.request(task,record,question_ids=['q2'])
        self.assert_reuse(agent,task,record,need)
        before = canonical(task)
        payload = json.loads(_writing_messages(task,[])[1]['content'])
        self.assertEqual(payload['research_context'],draft_research_context(task))
        self.assertTrue(any(row.get('data_need_id')==need['id'] for row in payload['research_context']['source_purposes']))
        self.assertEqual(canonical(task),before)
        self.assertEqual(task['ledger']['used']['model_calls'],0)

    def test_new_purposes_survive_store_save_and_reload(self):
        agent,task,record = self.official()
        self.assert_reuse(agent,task,record,self.request(task,record,question_ids=['q2']))
        expected = draft_research_context(task)
        agent.archive.save(task)
        restored = ResearchArchive(agent.archive.root).get(task['id'])
        self.assertEqual(draft_research_context(restored),expected)
        self.assertEqual(restored['ledger'],task['ledger'])
        self.assertEqual(next(row for row in restored['source_records'] if row['id']==record['id'])['snapshot'],record['snapshot'])

    def test_foreign_question_is_rejected_before_any_source_metadata_changes(self):
        agent,task,record = self.official()
        before = canonical(task)
        with self.assertRaises(ContractError):
            agent._acquire_sources(task,[self.request(task,record,question_ids=['other_task_question'])])
        self.assertEqual(canonical(task),before)
        agent.collector.external.assert_not_called()

    def test_a_changed_original_is_not_labeled_as_a_successful_reuse(self):
        agent,task,record = self.ifind()
        record['snapshot']['material']['parts'][0]['text'] += '\nChanged fixture; deliberately not rehashed.'
        before = deepcopy(record.get('request_purposes'))
        agent.collector.external.side_effect = ContractError('Controlled failed transport; no real request')
        agent._acquire_sources(task,[self.request(task,record)])
        agent.collector.external.assert_not_called()
        agent._gap.assert_called_once()
        self.assertEqual(record.get('request_purposes'),before)
        self.assertNotIn('reuse_original',record.get('data_need_ids',[]))
