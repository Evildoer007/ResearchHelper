"""Knowledge-bound locators improve reading without approving old studies."""
import copy
import hashlib
import json
import re
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import ROOT
from tests.probe_etf_extraction_context import payload_for
from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, canonical, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import literal_navigation_rule
from researchhelper.output_schema import financial_reference_prompt, original_batch_prompt, original_batch_output_schema
from researchhelper.planning import input_fingerprint
from researchhelper.reports import compose
from researchhelper.reasoning import model_object, MAX_CONTEXT_CHARACTERS
from researchhelper.source_reader import read, binding, validate_reading, reader_version_for, CONTEXT_LIMIT

POLICY = 'knowledge_reading_navigation_version'
RETAINED = ROOT / 'result/acceptance/one-sentence-research-xb3cycy7/task.json'


class KnowledgeReadingNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_hash = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.old = json.loads(RETAINED.read_text())
        cls.task = copy.deepcopy(cls.old)
        cls.task[POLICY] = 1
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.before = copy.deepcopy((cls.task['questions'], cls.task['ledger'], cls.task['data_need_specs']))
        cls.original_bundle = cls.old['reasoning_state']['reading']['bundle']
        with patch('socket.socket.connect', side_effect=AssertionError('Offline navigation replay')):
            cls.bundle = read(cls.task, cls.original_bundle['selection'], binding(cls.task), cls.original_bundle['artifact_ids'])
        cls.task['reasoning_state']['reading']['bundle'] = cls.bundle

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.original_hash
        assert (cls.task['questions'], cls.task['ledger'], cls.task['data_need_specs']) == cls.before

    def test_locked_mechanisms_and_method_entries_are_navigation_not_new_facts(self):
        rule = literal_navigation_rule(self.task)
        self.assertEqual(rule['version'], 2)
        self.assertFalse(rule['financial_approval'])
        self.assertFalse(rule['automatic_need_coverage'])
        entries = {item['id']:item for item in self.task['knowledge']['entries']}
        self.assertEqual({item['id'] for item in rule['knowledge_entries']}, {'rh_k_cash_profit','rh_k_net_margin'})
        for item in rule['knowledge_entries']:
            self.assertEqual(item['entry_hash'], fingerprint(entries[item['id']]))
        self.assertIn('经营性应收项目', rule['terms'])
        self.assertIn('收入确认', rule['terms'])
        self.assertNotIn('贵州茅台', canonical(rule))

    def test_same_original_selection_reads_mechanism_locations_within_the_original_limit(self):
        self.assertEqual(self.bundle['reader_version'], 6)
        self.assertEqual(self.bundle['selection'], self.original_bundle['selection'])
        self.assertEqual(self.bundle['artifact_ids'], self.original_bundle['artifact_ids'])
        self.assertLessEqual(len(canonical(self.bundle['views'])), CONTEXT_LIMIT)
        body = re.sub(r'\s+', '', ''.join(part['text'] for view in self.bundle['views'] for part in view.get('parts',[])))
        self.assertIn('经营性应收项目', body)
        self.assertIn('经营性应付项目', body)
        self.assertIn('收入确认', body)
        self.assertIn('合并利润表', body)
        self.assertIn('合并现金流量表', body)
        validate_reading(self.task, self.bundle)
        validate_reading(self.old, self.original_bundle)

    def test_financial_navigation_resolves_only_actual_same_source_quote_aliases(self):
        payload = payload_for(self.task)
        before = canonical(payload)
        shown = financial_reference_prompt(payload, self.task)
        rule = shown['knowledge_navigation']
        hits = 0
        for view in shown['untrusted_sources']:
            refs = {ref:quote for part in view['parts'] for ref,quote in part['quote_choices']}
            for index, reference in view.get('knowledge_quote_hits', []):
                self.assertIn(reference, refs)
                pattern = r'\s*'.join(re.escape(char) for char in rule['terms'][index])
                self.assertRegex(refs[reference], pattern)
                hits += 1
        self.assertGreater(hits, 0)
        self.assertEqual(canonical(payload), before)

    def test_batch_projection_retains_navigation_and_every_source(self):
        payload = payload_for(self.task)
        shown = original_batch_prompt(payload, self.task)
        self.assertEqual(shown['knowledge_navigation'], literal_navigation_rule(self.task))
        self.assertEqual(len(shown['untrusted_sources']), len(payload['untrusted_sources']))
        self.assertTrue(any(view.get('knowledge_quote_hits') for view in shown['untrusted_sources']))
        self.assertEqual(shown['questions'], payload['questions'])
        self.assertEqual(shown['calculations'], payload['calculations'])
        self.assertLessEqual(len(canonical(shown)) + len(canonical(original_batch_output_schema(self.task, payload['untrusted_sources']))), MAX_CONTEXT_CHARACTERS)

    def test_actual_financial_gateway_receives_navigation_after_protocol_selection(self):
        payload = payload_for(self.task)
        called = []
        def capture(task, messages, views):
            called.append(messages)
            body = json.loads(messages[-1]['content'])
            self.assertEqual(body['knowledge_navigation'], literal_navigation_rule(self.task))
            self.assertTrue(any(view.get('knowledge_quote_hits') for view in body['untrusted_sources']))
            self.assertEqual(body['questions'], payload['questions'])
            self.assertIn('knowledge_quote_hits', messages[0]['content'])
            raise RuntimeError('gateway_capture_only')
        model = SimpleNamespace(supports_structured_analysis=lambda: True, supports_financial_references=lambda: True,
            reason_structured_extraction=capture, reason_structured_financial_references=capture,
            reason_structured_original_batch=capture)
        with self.assertRaisesRegex(RuntimeError, 'gateway_capture_only'):
            model_object(model, self.task, 'extract', payload, {'evidence_specs'})
        self.assertEqual(len(called), 1)

    def test_legacy_or_invalid_policy_does_not_gain_financial_navigation(self):
        self.assertEqual(reader_version_for(self.old), 3)
        for value in (None, True, False, 0, 2, 1.0):
            task = copy.deepcopy(self.task)
            task[POLICY] = value
            task['input_hash'] = input_fingerprint(task)
            self.assertIsNone(literal_navigation_rule(task))

    def test_changed_knowledge_or_question_is_not_accepted_as_navigation(self):
        task = copy.deepcopy(self.task)
        task['knowledge']['entries'][0]['content'] += 'untrusted edit'
        with self.assertRaises(ContractError): literal_navigation_rule(task)
        task = copy.deepcopy(self.task)
        task['question'] = 'different question'
        with self.assertRaises(ContractError): literal_navigation_rule(task)

    def test_formal_policy_is_bound_and_frozen_without_external_calls(self):
        with tempfile.TemporaryDirectory(prefix='knowledge-navigation-policy-') as folder:
            agent = ResearchAgent(ResearchArchive(folder), ROOT, offline=True, require_argument_support=True)
            try:
                task = agent.create('解释ETF折溢价', entry='ETF', questions=[dict(id='q1',kind='fact',
                    question='解释ETF折溢价',answer_scope='general_definition',knowledge_needs=['rh_k_etf_premium_measures'])])
                self.assertEqual(task[POLICY], 1)
                old = copy.deepcopy(task)
                old.pop(POLICY)
                self.assertNotEqual(input_fingerprint(old), task['input_hash'])
                completed = agent.start(task['id'])
                self.assertEqual(compose(completed)[POLICY], 1)
                self.assertEqual(completed['ledger']['used']['model_calls'], 0)
            finally:
                agent.close_runtime()
