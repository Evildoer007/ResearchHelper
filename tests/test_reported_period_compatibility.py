"""A literal fiscal duration is not a point date or an invented calendar range.

Replay unchanged R117 proposals against their actual retained originals. Keep
the closed study failed and leave its required scope and financial values intact.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
from researchhelper.contracts import registered_financial_bindings
from researchhelper.data_requirements import period_matches, satisfies
from researchhelper.evidence import candidate_evidence
from researchhelper.analyzer import ResearchAnalyzer
from researchhelper.engine import ResearchAgent
from researchhelper.knowledge import ResearchKnowledge

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('RESEARCHHELPER_REPORTED_PERIOD_INPUT', str(
    ROOT/'result/acceptance/one-sentence-research-o9nv4a91/task.json')))


class ReportedPeriodCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_hash = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        cls.original = json.loads(SOURCE.read_text())

    def setUp(self):
        self.task = copy.deepcopy(self.original)
        guard = patch.object(socket.socket, 'connect', side_effect=AssertionError('Offline original replay'))
        guard.start()
        self.addCleanup(guard.stop)
        if os.environ.get('RESEARCHHELPER_FROZEN_TEST_EXECUTABLE'):
            from frozen_archive_test_loader import frozen_pdf_worker
            from researchhelper.material_parser import MaterialParser
            worker = patch.object(MaterialParser, 'worker_target', staticmethod(frozen_pdf_worker))
            worker.start()
            self.addCleanup(worker.stop)

    def tearDown(self):
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), self.source_hash)

    def test_literal_fiscal_labels_classify_as_durations_without_deriving_endpoints(self):
        for label in ('2026年半年度', '2026年上半年', '2026年度', '2026年年度', '2026年第一季度',
                      '2026年3季度', '2026年1-6月', '2026年1月至6月'):
            with self.subTest(label=label):
                self.assertTrue(period_matches(label, 'interval'))
                self.assertFalse(period_matches(label, 'point'))
                self.assertFalse(period_matches(label, 'month'))
        for label in ('本期', 'MRQ', '2026', '2026-06', '2026-06-30', '2026年6月30日',
                      '截至2026年6月30日', '2026年13-15月', '2026年6-1月'):
            self.assertFalse(period_matches(label, 'interval'))

    def test_three_actual_financial_inputs_survive_existing_source_and_financial_gates(self):
        before = copy.deepcopy(self.task)
        analyzer = ResearchAnalyzer()
        expected = dict(revenue=276916580, net_profit=47030638, operating_cash_flow=60216851)
        specs = [spec for spec in self.task['host_result']['evidence_specs']
                 if spec.get('calculation_context', {}).get('metric') in expected]
        self.assertEqual(len(specs), 3)
        for spec in specs:
            record = next(source for source in self.task['source_records'] if source['id'] == spec['artifact_id'])
            candidate = candidate_evidence(self.task, record, spec)
            adopted = analyzer.assess(self.task, candidate, spec['review'], self.task['source_records'])
            self.assertEqual(adopted['source_check'], '通过')
            self.assertEqual(adopted['applicability_check'], '通过')
            self.assertEqual(adopted['period'], '2026年半年度')
            self.assertEqual(adopted['observation_date'], '2026-06-30')
            self.assertEqual(adopted['raw_value'], expected[spec['calculation_context']['metric']])
            self.assertEqual(adopted['unit'], '千元')
        self.assertEqual(self.task, before)

    def test_period_shape_never_relaxes_unit_dimension_or_number_type(self):
        rule = dict(value_kind='number', unit_dimension='amount', period_kind='interval', minimum_count=1)
        item = dict(value=100, unit='千元', period='2026年半年度')
        self.assertTrue(satisfies(item, rule))
        for change in (dict(unit='%'), dict(value=None), dict(value=True), dict(period='2026-06-30')):
            self.assertFalse(satisfies(dict(item, **change), rule))

    def test_verified_raw_inputs_execute_both_registered_calculations_without_approving_an_answer(self):
        analyzer = ResearchAnalyzer()
        ledger = copy.deepcopy(self.task['ledger'])
        for spec in self.task['host_result']['evidence_specs']:
            if spec.get('calculation_context', {}).get('metric') not in (
                    'revenue', 'net_profit', 'operating_cash_flow'):
                continue
            record = next(source for source in self.task['source_records'] if source['id'] == spec['artifact_id'])
            evidence = analyzer.assess(self.task, candidate_evidence(self.task, record, spec),
                                       spec['review'], self.task['source_records'])
            self.task['evidence'].append(evidence)
        proposal = registered_financial_bindings(self.task, [q['id'] for q in self.task['questions']])
        self.assertIsNotNone(proposal)
        agent = ResearchAgent.__new__(ResearchAgent)
        agent.knowledge = ResearchKnowledge()
        for spec in proposal['calculations']:
            agent._calculate_spec(self.task, spec)
        results = {item['method']: item for item in self.task['calculations']}
        self.assertEqual(set(results), {'net_profit_margin_v1', 'operating_cash_profit_ratio_v1'})
        self.assertAlmostEqual(results['net_profit_margin_v1']['output']['value'],
                               47030638 / 276916580 * 100)
        self.assertAlmostEqual(results['operating_cash_profit_ratio_v1']['output']['value'],
                               60216851 / 47030638 * 100)
        self.assertTrue(all(item['parameters']['period'] == '2026年半年度' for item in results.values()))
        self.assertEqual(self.task['ledger'], ledger)
        self.assertFalse(self.task.get('argument_support_reviews'))
        self.assertTrue(all(question['status'] != '已回答' for question in self.task['questions']))


if __name__ == '__main__':
    unittest.main()
