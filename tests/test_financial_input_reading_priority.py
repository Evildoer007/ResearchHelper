"""Read complete registered statement groups before optional keyword hits."""
import copy
import hashlib
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests import ROOT
from researchhelper.contracts import canonical
from researchhelper.archive import ResearchArchive
from researchhelper.engine import ResearchAgent
from researchhelper.reports import compose
from tests.probe_etf_extraction_context import payload_for
from researchhelper.planning import input_fingerprint
from researchhelper.reasoning import evidence_contract, model_object
from researchhelper import source_reader
from researchhelper.source_reader import read, binding, validate_reading, CONTEXT_LIMIT

POLICY = 'financial_input_reading_priority_version'
RETAINED = ROOT / 'result/acceptance/one-sentence-research-0f7nj3bq/task.json'


class FinancialInputReadingPriorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_hash = hashlib.sha256(RETAINED.read_bytes()).hexdigest()
        cls.old = json.loads(RETAINED.read_text())
        cls.old_bundle = cls.old['reasoning_state']['reading']['bundle']
        cls.task = copy.deepcopy(cls.old)
        cls.task[POLICY] = 1
        cls.task['input_hash'] = input_fingerprint(cls.task)
        cls.before = copy.deepcopy((cls.task['questions'], cls.task['ledger'], cls.task['data_need_specs']))
        with patch('socket.socket.connect', side_effect=AssertionError('Offline statement reading')):
            cls.bundle = read(cls.task, cls.old_bundle['selection'], binding(cls.task), cls.old_bundle['artifact_ids'])

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(RETAINED.read_bytes()).hexdigest() == cls.raw_hash
        assert (cls.task['questions'], cls.task['ledger'], cls.task['data_need_specs']) == cls.before

    def test_required_original_metric_is_not_crowded_out_by_mechanism_keywords(self):
        old = evidence_contract(self.old, self.old_bundle['views'])
        before = {cell['context']['metric'] for source in old['sources'] for cell in source.get('financial_context_cells', [])}
        self.assertNotIn('net_profit', before)
        current = evidence_contract(self.task, self.bundle['views'])
        after = {cell['context']['metric'] for source in current['sources'] for cell in source.get('financial_context_cells', [])}
        self.assertTrue({'revenue', 'net_profit', 'operating_cash_flow'}.issubset(after))

    def test_original_continuation_table_and_its_unit_header_are_read_together(self):
        parts = {part['locator']:part for view in self.bundle['views'] for part in view.get('parts', [])}
        for locator in ('第30页', '第30页表2', '第31页表1'):
            self.assertIn(locator, parts)
            self.assertEqual(parts[locator]['reading_start'], 0)
            self.assertEqual(parts[locator]['reading_end'], parts[locator]['original_characters'])

    def test_all_reading_stays_bounded_and_exact_without_dropping_mechanism_material(self):
        self.assertLessEqual(len(canonical(self.bundle['views'])), CONTEXT_LIMIT)
        text = ''.join(part['text'] for view in self.bundle['views'] for part in view.get('parts', []))
        self.assertIn('经营性应收项目', text)
        self.assertIn('经营性应付项目', text)
        trace = next(row for row in self.bundle['coverage'] if row.get('registered_input_reading'))['registered_input_reading']
        self.assertFalse(trace['financial_approval'])
        self.assertFalse(trace['absence_is_proof'])
        self.assertTrue(any('net_profit' in group['metrics'] and group['read_in_full'] for group in trace['groups']))
        validate_reading(self.task, self.bundle)
        validate_reading(self.old, self.old_bundle)

    def test_old_or_invalid_policy_keeps_the_original_reading_byte_identical(self):
        for value in (None, True, False, 0, 2, 1.0):
            task = copy.deepcopy(self.old)
            if value is not None:
                task[POLICY] = value
            task['input_hash'] = input_fingerprint(task)
            with self.subTest(value=value):
                replay = read(task, self.old_bundle['selection'], binding(task), self.old_bundle['artifact_ids'])
                # Bindings differ for explicit invalid flags, but windows and
                # coverage must retain the former selection, not gain priority.
                self.assertEqual(replay['views'], self.old_bundle['views'])
                self.assertEqual(replay['coverage'], self.old_bundle['coverage'])

    def test_too_large_groups_remain_unread_rather_than_increasing_the_limit(self):
        with patch.object(source_reader, 'CONTEXT_LIMIT', 2048):
            small = read(self.task, self.old_bundle['selection'], binding(self.task), self.old_bundle['artifact_ids'])
        self.assertLessEqual(len(canonical(small['views'])), 2048)
        trace = next(row for row in small['coverage'] if row.get('registered_input_reading'))['registered_input_reading']
        self.assertTrue(any(not group['read_in_full'] for group in trace['groups']))
        self.assertFalse(trace['financial_approval'])

    def test_complete_inputs_and_navigation_fit_the_real_extraction_preflight(self):
        task = copy.deepcopy(self.task)
        task['reasoning_state']['reading']['bundle'] = self.bundle
        payload = payload_for(task)
        calls = []
        def capture(task, messages, views):
            calls.append(messages)
            content = json.loads(messages[-1]['content'])
            self.assertEqual(content['questions'], payload['questions'])
            self.assertTrue(any(view.get('knowledge_quote_hits') for view in content['untrusted_sources']))
            raise RuntimeError('gateway_capture_only')
        model = SimpleNamespace(supports_structured_analysis=lambda: True, supports_financial_references=lambda: True,
            reason_structured_extraction=capture, reason_structured_financial_references=capture,
            reason_structured_original_batch=capture)
        with self.assertRaisesRegex(RuntimeError, 'gateway_capture_only'):
            model_object(model, task, 'extract', payload, {'evidence_specs'})
        self.assertEqual(len(calls), 1)

    def test_new_formal_policy_is_input_bound_and_frozen(self):
        with tempfile.TemporaryDirectory(prefix='financial-input-reading-policy-') as folder:
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
