"""Independent evaluation contract tests; no original tests are overwritten."""
import copy
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from researchhelper.archive import ResearchArchive
from researchhelper.contracts import ContractError, fingerprint
from researchhelper.engine import ResearchAgent
from researchhelper.planning import input_fingerprint
from tests import research_quality_evaluation_v2 as evaluator

ROOT = Path(__file__).resolve().parents[2]


class ResearchQualityEvaluationV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='evaluation-v2-', dir=ROOT / 'result/acceptance')
        cls.blocker = patch.object(socket.socket, 'connect', side_effect=AssertionError('禁止联网'))
        cls.blocker.start()
        agent = ResearchAgent(ResearchArchive(cls.folder.name), ROOT, offline=True)
        task = agent.create_case('etf')
        cls.good = agent.start(task['id'])
        assert cls.good['status'] == '已完成'
        cls.saved = agent.archive.report(task['id'], 1)

    @classmethod
    def tearDownClass(cls):
        cls.blocker.stop()
        cls.folder.cleanup()

    def setUp(self):
        self.task, self.saved = copy.deepcopy(self.good), copy.deepcopy(self.__class__.saved)
        self.spec = {'id': 'etf-fixed-development', 'split': 'development',
            'input_digest': fingerprint(evaluator.input_scope(self.task)), 'expected_status': '已完成',
            'question_states': {q['id']: '已回答' for q in self.task['questions'] if q['required']}}

    def evaluate(self):
        return evaluator.evaluate(self.task, self.spec, saved_delivery=self.saved)

    def test_current_and_historical_scope_cannot_share_specification(self):
        self.task['research_mode'] = 'current' if self.task.get('research_mode') != 'current' else 'historical'
        with self.assertRaises(ContractError):
            self.evaluate()

    def test_changed_context_cannot_share_specification(self):
        self.task['conversation_context'] = {'version': 1, 'reference': 'changed parent'}
        with self.assertRaises(ContractError):
            self.evaluate()

    def test_changed_registered_plan_is_not_an_unchanged_input(self):
        self.task['questions'][0]['evidence_needs'].append('未登记的新需求')
        self.assertIn('invalid_locked_input', self.evaluate()['critical_violations'])

    def test_changed_html_cannot_pass_from_a_nonempty_body(self):
        self.saved['html'] += '<!--changed-->'
        result = self.evaluate()
        self.assertEqual(result['functional_status'], 'failed')
        self.assertIn('saved_html_integrity_failed', result['critical_violations'])

    def test_report_cannot_borrow_a_different_research_mode(self):
        self.saved['content']['research_mode'] = 'current' if self.task.get('research_mode') != 'current' else 'historical'
        self.saved['digest'] = fingerprint(self.saved['content'])
        self.task['delivery']['digest'] = self.saved['digest']
        self.assertIn('saved_research_context_mismatch', self.evaluate()['critical_violations'])

    def test_report_cannot_borrow_different_raw_source_records(self):
        self.saved['content']['source_records'] = [{'id': 'foreign_snapshot'}]
        self.saved['digest'] = fingerprint(self.saved['content'])
        self.task['delivery']['digest'] = self.saved['digest']
        self.assertIn('saved_research_context_mismatch', self.evaluate()['critical_violations'])

    def test_legacy_html_is_not_retroactively_verified(self):
        self.task['delivery'].pop('html_sha256')
        result = self.evaluate()
        self.assertEqual(result['saved_html_integrity'], 'legacy_unverified')
        self.assertFalse(result['independent_research_quality_verified'])

    def test_original_specification_and_task_are_not_modified(self):
        before = fingerprint(self.task), fingerprint(self.spec)
        self.evaluate()
        self.assertEqual(before, (fingerprint(self.task), fingerprint(self.spec)))

    def test_problem_identity_excludes_run_and_plan_counters(self):
        before = fingerprint(evaluator.input_scope(self.task))
        self.task['batch'] += 1
        self.task['version'] += 1
        self.task['plan_version'] += 1
        self.assertEqual(before, fingerprint(evaluator.input_scope(self.task)))

    def test_historical_missing_mode_is_not_silently_current(self):
        self.task.pop('research_mode', None)
        legacy = fingerprint(evaluator.input_scope(self.task))
        self.task['research_mode'] = 'current'
        self.assertNotEqual(legacy, fingerprint(evaluator.input_scope(self.task)))

    def test_repeated_cumulative_ledger_is_not_new_supplier_usage(self):
        record = self.evaluate()
        result = evaluator.aggregate([record, dict(record, case_id='recovery-repeat')])
        self.assertEqual(result['unique_input_count'], 1)
        self.assertEqual(result['cost_basis'], 'sum_of_cumulative_run_ledgers_not_unique_network_usage')

    def test_full_input_digest_uses_actual_case_specification(self):
        result = self.evaluate()
        self.assertEqual(result['case_specification_hash'], fingerprint(self.spec))
        self.assertEqual(result['input_digest'], self.spec['input_digest'])
        self.assertEqual(result['effective_input_digest'], input_fingerprint(self.task))

    def test_old_evaluation_record_cannot_be_relabelled(self):
        record = self.evaluate()
        record['evaluation_version'] = 1
        with self.assertRaises(ContractError):
            evaluator.aggregate([record])

    def test_invalid_delivery_metadata_is_a_failed_record(self):
        self.task['delivery'] = 'invalid'
        result = self.evaluate()
        self.assertEqual(result['functional_status'], 'failed')
        self.assertIn('missing_saved_delivery', result['critical_violations'])
